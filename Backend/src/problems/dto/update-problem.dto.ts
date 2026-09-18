import { ApiPropertyOptional } from '@nestjs/swagger';
import { Difficulty } from '@prisma/client';
import { Type } from 'class-transformer';
import { IsEnum, IsInt, IsOptional, IsString, Max, Min, MinLength } from 'class-validator';

export class UpdateProblemDto {
  @ApiPropertyOptional()
  @IsOptional()
  @IsString()
  @MinLength(3)
  title?: string;

  @ApiPropertyOptional()
  @IsOptional()
  @IsString()
  description?: string;

  @ApiPropertyOptional({ enum: Difficulty })
  @IsOptional()
  @IsEnum(Difficulty)
  difficulty?: Difficulty;

  @ApiPropertyOptional()
  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(1)
  points?: number;

  @ApiPropertyOptional()
  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(100)
  timeLimitMs?: number;

  @ApiPropertyOptional()
  @IsOptional()
  @Type(() => Number)
  @IsInt()
  @Min(16)
  @Max(1024)
  memoryLimitMb?: number;

  @ApiPropertyOptional()
  @IsOptional()
  @IsString()
  inputFormat?: string;

  @ApiPropertyOptional()
  @IsOptional()
  @IsString()
  outputFormat?: string;
}
