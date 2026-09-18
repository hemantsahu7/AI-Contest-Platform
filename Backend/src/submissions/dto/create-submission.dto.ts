import { ApiProperty } from '@nestjs/swagger';
import { IsIn, IsString, MaxLength } from 'class-validator';

export class CreateSubmissionDto {
  @ApiProperty({ example: '#include <iostream>\\nint main() { return 0; }' })
  @IsString()
  @MaxLength(102400)
  sourceCode!: string;

  @ApiProperty({ example: 'cpp' })
  @IsIn(['cpp'])
  language!: 'cpp';
}
